import { ArrowLeft, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import type { BenchmarkExperimentComparisonOut, BenchmarkMatchPageOut, BenchmarkMetricsOut, BenchmarkOverviewOut } from "./api/generated";
import { ApiError, api } from "./api/client";
import { copy, Language } from "./copy";

type BenchmarkLoadState = "loading" | "ready" | "empty" | "error";
type JsonRecord = Record<string, unknown>;
type BenchmarkCopy = typeof copy[Language];

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function textValue(value: unknown, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function numberValue(value: unknown, fallback = "—") {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function percentValue(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function statusTone(value: string): "success" | "warning" | "info" {
  if (["READY", "COMPLETED", "AVAILABLE", "PASSED", "PASS", "ผ่าน", "QUALIFIED_FOR_PILOT"].includes(value)) return "success";
  if (["EMPTY", "INCOMPLETE", "NOT_RUN", "NOT_QUALIFIED", "HOLDOUT_INSUFFICIENT", "UNAVAILABLE", "NO_MATCHED_IDENTITIES", "STRUCTURALLY_INVALID", "STALE", "NOT_SCORABLE", "HOLD", "รอ"].includes(value)) return "warning";
  return "info";
}

function BenchmarkBadge({ value }: { value: string }) {
  return <span className={`badge ${statusTone(value)}`}><span aria-hidden="true" />{value}</span>;
}

function MetricTable({ label, rows }: { label: string; rows: Array<[string, string]> }) {
  return (
    <section className="benchmark-panel">
      <div className="section-heading compact"><h2>{label}</h2></div>
      <table className="benchmark-metric-table" aria-label={label}>
        <tbody>{rows.map(([name, value]) => <tr key={name}><th scope="row">{name}</th><td className="mono">{value}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

function metricRows(t: BenchmarkCopy, metrics: JsonRecord) {
  return [
    [t.benchmarkPrecision, percentValue(metrics.precision)],
    [t.benchmarkRecall, percentValue(metrics.recall)],
    [t.benchmarkF1, percentValue(metrics.f1)],
    [t.benchmarkFalsePositive, numberValue(metrics.false_positive)],
    [t.benchmarkFalseNegative, numberValue(metrics.false_negative)],
    [t.benchmarkDuplicates, numberValue(metrics.duplicate_automatic)],
  ] as Array<[string, string]>;
}

export function BenchmarkWorkspace({
  t,
  overview,
  state,
  error,
  onReload,
  onBack
}: {
  t: BenchmarkCopy;
  overview: BenchmarkOverviewOut | null;
  state: BenchmarkLoadState;
  error: string;
  onReload: () => void;
  onBack: () => void;
}) {
  const [metricsState, setMetricsState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [metricsError, setMetricsError] = useState("");
  const [metrics, setMetrics] = useState<BenchmarkMetricsOut | null>(null);
  const [matchesState, setMatchesState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [matchesError, setMatchesError] = useState("");
  const [matches, setMatches] = useState<BenchmarkMatchPageOut | null>(null);
  const [experimentsState, setExperimentsState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [experimentsError, setExperimentsError] = useState("");
  const [experiments, setExperiments] = useState<BenchmarkExperimentComparisonOut[]>([]);
  const latestRun = overview?.latest_run ?? null;

  useEffect(() => {
    let cancelled = false;
    if (!latestRun?.id) {
      setMetrics(null);
      setMetricsState("idle");
      return () => {
        cancelled = true;
      };
    }
    setMetricsState("loading");
    setMetricsError("");
    void api.benchmarkMetrics(latestRun.id)
      .then((payload) => {
        if (cancelled) return;
        setMetrics(payload);
        setMetricsState("ready");
      })
      .catch((cause) => {
        if (cancelled) return;
        setMetricsState("error");
        setMetricsError(cause instanceof ApiError ? cause.message : "benchmark_metrics_failed");
      });
    return () => {
      cancelled = true;
    };
  }, [latestRun?.id]);

  useEffect(() => {
    let cancelled = false;
    if (!latestRun?.id) {
      setMatches(null);
      setMatchesState("idle");
      return () => {
        cancelled = true;
      };
    }
    setMatchesState("loading");
    setMatchesError("");
    void api.benchmarkMatches(latestRun.id)
      .then((payload) => {
        if (cancelled) return;
        setMatches(payload);
        setMatchesState("ready");
      })
      .catch((cause) => {
        if (cancelled) return;
        setMatchesState("error");
        setMatchesError(cause instanceof ApiError ? cause.message : "benchmark_matches_failed");
      });
    return () => {
      cancelled = true;
    };
  }, [latestRun?.id]);

  useEffect(() => {
    let cancelled = false;
    if (state !== "ready") {
      setExperiments([]);
      setExperimentsState("idle");
      return () => {
        cancelled = true;
      };
    }
    setExperimentsState("loading");
    setExperimentsError("");
    void api.benchmarkExperiments()
      .then((payload) => {
        if (cancelled) return;
        setExperiments(payload);
        setExperimentsState("ready");
      })
      .catch((cause) => {
        if (cancelled) return;
        setExperimentsState("error");
        setExperimentsError(cause instanceof ApiError ? cause.message : "benchmark_experiments_failed");
      });
    return () => {
      cancelled = true;
    };
  }, [state]);

  const corpus = overview?.corpus_revisions?.[0];
  const sources = overview?.corpus_revisions?.flatMap((revision) => revision.sources ?? []) ?? [];
  const metricPayload = asRecord(metrics?.metrics);
  const eventMetrics = asRecord(metricPayload.event_metrics);
  const countMetrics = asRecord(metricPayload.count_metrics);
  const directionMetrics = asRecord(metricPayload.direction_metrics);
  const classMetrics = asRecord(metricPayload.class_metrics);
  const timestampMetrics = asRecord(metricPayload.timestamp_metrics);
  const fragmentation = asRecord(metricPayload.fragmentation_metrics);
  const throughput = asRecord(metricPayload.throughput_metrics);
  const qualification = asRecord(metrics?.qualification);
  const gates = asRecord(qualification.gates);
  const thresholdEvaluation = asRecord(qualification.threshold_evaluation);
  const thresholdResults = Array.isArray(thresholdEvaluation.results) ? thresholdEvaluation.results.map(asRecord) : [];
  const thresholdPolicy = asRecord(qualification.policy);
  const thresholdPolicyPresent = qualification.threshold_policy_present === true || Object.keys(thresholdPolicy).length > 0;
  const blockers = Array.isArray(qualification.automatic_result_blockers) ? qualification.automatic_result_blockers.map(String) : [];
  const automaticResultState = blockers.includes("STALE_AUTOMATIC_RESULT")
    ? "STALE"
    : blockers.includes("STRUCTURALLY_INVALID_AUTOMATIC_RESULT") || blockers.some((blocker) => blocker.includes("RECONCILIATION"))
      ? "STRUCTURALLY_INVALID"
      : blockers.length > 0
        ? "NOT_SCORABLE"
        : "STRUCTURALLY_VALID";
  const diagnosticRows = matches?.items ?? [];

  return (
    <section className="benchmark-workspace" aria-labelledby="benchmark-title">
      <div className="benchmark-header">
        <div>
          <p className="eyebrow">{t.benchmarkEyebrow}</p>
          <h1 id="benchmark-title">{t.benchmarkTitle}</h1>
          <p className="benchmark-lede">{t.benchmarkBody}</p>
        </div>
        <div className="benchmark-header-actions">
          <button className="secondary" onClick={onBack}><ArrowLeft size={16} aria-hidden="true" />{t.benchmarkBack}</button>
          <button className="primary" onClick={onReload} disabled={state === "loading"}>
            <RefreshCw size={16} aria-hidden="true" />{t.benchmarkReload}
          </button>
        </div>
      </div>

      {error && <div className="banner warning"><ShieldCheck size={18} aria-hidden="true" /><div><strong>{t.errorTitle}</strong><span>{error}</span></div></div>}
      {state === "loading" && <div className="benchmark-state" role="status"><strong>{t.loading}</strong><span>{t.loadingBody}</span></div>}
      {state === "error" && <div className="benchmark-state warning" role="alert"><strong>{t.backendUnavailable}</strong><span>{error || "benchmark_overview_failed"}</span></div>}
      {state === "empty" && (
        <div className="benchmark-empty" role="status">
          <div className="benchmark-empty-mark">6C</div>
          <div><h2>{t.benchmarkEmptyTitle}</h2><p>{t.benchmarkEmptyBody}</p></div>
          <p className="benchmark-empty-note">{t.benchmarkNoPolicy}</p>
        </div>
      )}

      {overview && state === "ready" && (
        <>
          <section className="benchmark-ledger" aria-labelledby="benchmark-ledger-title">
            <div className="section-heading compact"><div><p className="eyebrow">{t.benchmarkCorpus}</p><h2 id="benchmark-ledger-title">{textValue(corpus?.revision)}</h2></div><BenchmarkBadge value={overview.status} /></div>
            <dl className="benchmark-evidence-grid">
              <div><dt>{t.benchmarkSources}</dt><dd className="mono">{overview.source_count}</dd></div>
              <div><dt>{t.benchmarkRights}</dt><dd>{(Object.entries(overview.rights_coverage ?? {}) as Array<[string, number]>).map(([key, value]) => <span key={key} className="benchmark-inline-value"><b>{value}</b> {key}</span>)}</dd></div>
              <div><dt>{t.benchmarkSplits}</dt><dd>{(Object.entries(overview.split_counts ?? {}) as Array<[string, number]>).map(([key, value]) => <span key={key} className="benchmark-inline-value"><b>{value}</b> {key}</span>)}</dd></div>
              <div><dt>{t.benchmarkLatestRun}</dt><dd>{latestRun ? <BenchmarkBadge value={latestRun.qualification_status} /> : t.benchmarkNoRun}</dd></div>
            </dl>
          </section>

          <section className="benchmark-panel benchmark-source-panel" aria-labelledby="benchmark-sources-title">
            <div className="section-heading compact"><div><p className="eyebrow">{t.benchmarkSources}</p><h2 id="benchmark-sources-title">{t.benchmarkRights}</h2></div></div>
            <div className="benchmark-table-wrap">
              <table className="benchmark-source-table" aria-label={t.benchmarkSources}>
                <thead><tr><th scope="col">source</th><th scope="col">split</th><th scope="col">{t.benchmarkRightsStatus}</th><th scope="col">conditions</th><th scope="col">checksum</th></tr></thead>
                <tbody>{sources.map((source) => <tr key={source.id}><th scope="row" className="mono">{source.benchmark_source_id}</th><td><BenchmarkBadge value={source.benchmark_split} /></td><td><BenchmarkBadge value={source.rights_status} /></td><td>{(source.condition_tags ?? []).join(" · ") || "—"}</td><td>{source.checksum_verified ? "verified" : "unverified"}</td></tr>)}</tbody>
              </table>
            </div>
          </section>

          <section className="benchmark-panel benchmark-experiment-panel" aria-labelledby="benchmark-experiment-title">
            <div className="section-heading compact"><div><p className="eyebrow">{t.benchmarkExperiment}</p><h2 id="benchmark-experiment-title">{t.benchmarkExperiment}</h2></div></div>
            {experimentsState === "loading" && <div className="benchmark-state" role="status"><strong>{t.loading}</strong><span>{t.loadingBody}</span></div>}
            {experimentsState === "error" && <div className="benchmark-state warning" role="alert"><strong>{t.errorTitle}</strong><span>{experimentsError}</span></div>}
            {experimentsState === "ready" && experiments.length === 0 && <div className="benchmark-empty compact" role="status"><div><h3>{t.benchmarkExperimentNoRun}</h3><p>{t.benchmarkQualificationBody}</p></div></div>}
            {experimentsState === "ready" && experiments.length > 0 && <div className="benchmark-experiment-list">{experiments.map((experiment) => (
              <div className="benchmark-experiment-row" key={experiment.suite_id}>
                <div><strong className="mono">{experiment.suite_revision}</strong><span>{experiment.corpus_revision}</span></div>
                <dl><div><dt>{t.benchmarkExperimentStatus}</dt><dd><BenchmarkBadge value={experiment.status} /></dd></div><div><dt>{t.benchmarkExperimentCandidates}</dt><dd>{(experiment.candidate_configurations ?? []).length}</dd></div><div><dt>{t.benchmarkExperimentRuns}</dt><dd>{(experiment.runs ?? []).length}</dd></div><div><dt>{t.benchmarkExperimentPareto}</dt><dd>{textValue(asRecord(experiment.pareto).status)}</dd></div></dl>
              </div>
            ))}</div>}
          </section>

          {!latestRun && <div className="benchmark-empty compact" role="status"><div><h2>{t.benchmarkNoRun}</h2><p>{t.benchmarkEmptyBody}</p></div></div>}
          {latestRun && (
            <>
              <section className="benchmark-run-strip" aria-labelledby="benchmark-run-title">
                <div><p className="eyebrow">{t.benchmarkLatestRun}</p><h2 id="benchmark-run-title" className="mono">{latestRun.id}</h2></div>
                <dl><div><dt>{t.benchmarkRunStatus}</dt><dd><BenchmarkBadge value={latestRun.status} /></dd></div><div><dt>{t.benchmarkAutomaticResult}</dt><dd><BenchmarkBadge value={automaticResultState} /></dd></div><div><dt>{t.benchmarkQualificationStatus}</dt><dd><BenchmarkBadge value={latestRun.qualification_status} /></dd></div><div><dt>{t.benchmarkSourcePts}</dt><dd className="mono">{latestRun.benchmark_source_id}</dd></div></dl>
              </section>
              {latestRun.status === "INCOMPLETE" && <div className="benchmark-state warning" role="alert"><strong>{t.benchmarkIncomplete}</strong>{blockers.length > 0 && <><span>{t.benchmarkBlockers}</span><ul>{blockers.map((blocker) => <li key={blocker} className="mono">{blocker}</li>)}</ul></>}</div>}
              {metricsState === "loading" && <div className="benchmark-state" role="status"><strong>{t.benchmarkMetricsPending}</strong></div>}
              {metricsState === "error" && <div className="benchmark-state warning" role="alert"><strong>{t.errorTitle}</strong><span>{metricsError}</span></div>}
              {metricsState === "ready" && (
                <>
                  {latestRun.status !== "INCOMPLETE" && <div className="benchmark-metric-grid">
                    <MetricTable label={t.benchmarkMetricEvent} rows={metricRows(t, eventMetrics)} />
                    <MetricTable label={t.benchmarkMetricCount} rows={[
                      [t.count, numberValue(asRecord(countMetrics.total).automatic_count)],
                      ["ground truth", numberValue(asRecord(countMetrics.total).ground_truth_count)],
                      ["signed error", numberValue(asRecord(countMetrics.total).signed_error)],
                      [t.benchmarkMisses, numberValue(asRecord(metricPayload.duplicate_metrics).missed_events)]
                    ]} />
                    <MetricTable label={t.benchmarkMetricDirection} rows={[[t.benchmarkDirectionAccuracy, percentValue(directionMetrics.direction_accuracy)], ["direction errors", numberValue(directionMetrics.direction_errors)], ["denominator", numberValue(directionMetrics.denominator)]]} />
                    <MetricTable label={t.benchmarkMetricClass} rows={[["macro F1", percentValue(classMetrics.macro_f1)], ["weighted F1", percentValue(classMetrics.weighted_f1)], [t.benchmarkUnknownRate, percentValue(classMetrics.unknown_ambiguous_rate)], ["matched support", numberValue(classMetrics.matched_support)]]} />
                    <MetricTable label={t.benchmarkMetricTimestamp} rows={[[t.benchmarkTimestampMae, `${numberValue(timestampMetrics.mean_absolute_error_ms)} ms`], [t.benchmarkTimestampP95, `${numberValue(timestampMetrics.p95_absolute_error_ms)} ms`], ["authority", textValue(timestampMetrics.time_authority)]]} />
                    <MetricTable label={t.benchmarkMetricFragmentation} rows={[[t.benchmarkFragmentationAvailability, textValue(fragmentation.availability)], ["fragmentation rate", percentValue(fragmentation.fragmented_ground_truth_rate)], ["short tracks", numberValue(fragmentation.short_track_count)]]} />
                    <MetricTable label={t.benchmarkMetricThroughput} rows={[[t.benchmarkProcessingRatio, numberValue(throughput.processing_duration_video_duration_ratio)], [t.benchmarkRealTime, textValue(throughput.real_time_criterion)], ["device", textValue(throughput.resolved_device)]]} />
                  </div>}
                  <section className="benchmark-panel benchmark-qualification" aria-labelledby="benchmark-qualification-title">
                    <div className="section-heading compact"><div><p className="eyebrow">{t.benchmarkQualification}</p><h2 id="benchmark-qualification-title">{qualification.status ? textValue(qualification.status) : textValue(latestRun.qualification_status)}</h2></div><BenchmarkBadge value={qualification.status ? textValue(qualification.status) : latestRun.qualification_status} /></div>
                    <p>{t.benchmarkQualificationBody}</p>
                    <div className="benchmark-gates">{Object.entries(gates).map(([name, value]) => { const gate = asRecord(value); const passed = gate.passed === true; return <div key={name} className={passed ? "gate passed" : "gate blocked"}><span aria-hidden="true" /> <span>{name}<small>{textValue(gate.reason, "")}</small></span><strong>{passed ? "PASS" : "HOLD"}</strong></div>; })}</div>
                    {thresholdResults.length > 0 && <div className="benchmark-table-wrap benchmark-threshold-table-wrap"><table className="benchmark-diagnostic-table" aria-label={t.benchmarkThresholds}><thead><tr><th scope="col">{t.benchmarkThresholdMetric}</th><th scope="col">{t.benchmarkThresholdObserved}</th><th scope="col">{t.benchmarkThresholdRequired}</th><th scope="col">{t.benchmarkThresholdAvailability}</th><th scope="col">{t.benchmarkThresholdResult}</th><th scope="col">{t.benchmarkThresholdReason}</th></tr></thead><tbody>{thresholdResults.map((threshold, index) => { const passed = threshold.passed === true; return <tr key={`${String(threshold.metric_path)}-${index}`}><th scope="row" className="mono">{textValue(threshold.metric_path)}</th><td className="mono">{textValue(threshold.observed_value)}</td><td className="mono">{textValue(threshold.operator)} {textValue(threshold.required_value)} {textValue(threshold.unit)}</td><td className="mono">{textValue(threshold.availability)}</td><td><BenchmarkBadge value={passed ? t.benchmarkThresholdPass : t.benchmarkThresholdFail} /></td><td>{textValue(threshold.failure_reason, "—")}</td></tr>; })}</tbody></table></div>}
                    {thresholdPolicyPresent && <p className="benchmark-disclosure">{t.benchmarkThresholdPolicy}: <span className="mono">{textValue(thresholdPolicy.policy_revision)}</span></p>}
                    {!thresholdPolicyPresent && <p className="benchmark-disclosure">{t.benchmarkNoPolicy}</p>}
                  </section>
                  <section className="benchmark-panel benchmark-diagnostics" aria-labelledby="benchmark-diagnostics-title">
                    <div className="section-heading compact"><div><p className="eyebrow">{t.benchmarkDiagnostics}</p><h2 id="benchmark-diagnostics-title">{t.benchmarkDiagnostics}</h2><p>{t.benchmarkDiagnosticsBody}</p></div></div>
                    {matchesState === "loading" && <div className="benchmark-state" role="status"><strong>{t.loading}</strong><span>{t.loadingBody}</span></div>}
                    {matchesState === "error" && <div className="benchmark-state warning" role="alert"><strong>{t.errorTitle}</strong><span>{matchesError}</span></div>}
                    {matchesState === "ready" && diagnosticRows.length === 0 && <div className="benchmark-empty compact" role="status"><div><h3>{t.benchmarkDiagnosticNoRows}</h3></div></div>}
                    {matchesState === "ready" && diagnosticRows.length > 0 && <div className="benchmark-table-wrap"><table className="benchmark-diagnostic-table" aria-label={t.benchmarkDiagnostics}>
                      <thead><tr><th scope="col">{t.benchmarkDiagnosticCategory}</th><th scope="col">{t.benchmarkDiagnosticAutomaticId}</th><th scope="col">{t.benchmarkDiagnosticGroundTruthId}</th><th scope="col">{t.benchmarkDiagnosticTimestampError}</th><th scope="col">{t.benchmarkDiagnosticFlags}</th></tr></thead>
                      <tbody>{diagnosticRows.map((row, index) => <tr key={String(row.id ?? `${row.primary_category}-${index}`)}><th scope="row"><BenchmarkBadge value={textValue(row.primary_category)} /></th><td className="mono">{textValue(row.automatic_event_id)}</td><td className="mono">{textValue(row.ground_truth_event_id)}</td><td className="mono">{numberValue(row.timestamp_error_ms)} ms</td><td className="mono">{textValue(row.secondary_error_flags)}</td></tr>)}</tbody>
                    </table></div>}
                  </section>
                </>
              )}
            </>
          )}
          <details className="benchmark-limitations"><summary>{t.benchmarkLimitations}</summary><ul>{(overview.limitations ?? []).map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></details>
        </>
      )}
    </section>
  );
}
