/* global Buffer, document, getComputedStyle, process, window */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../../..");

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.clear());
});

function ffmpegPath() {
  const executable = process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg";
  const repositoryLocal = path.join(repoRoot, ".local-tools", "ffmpeg", "bin", executable);
  return fs.existsSync(repositoryLocal) ? repositoryLocal : executable;
}

function legalFixture() {
  const fixture = path.join(repoRoot, ".local-data", "playwright-fixtures", "e2e-legal-upload.mp4");
  fs.mkdirSync(path.dirname(fixture), { recursive: true });
  if (!fs.existsSync(fixture)) {
    try {
      execFileSync(ffmpegPath(), [
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x180:rate=10:duration=2",
        "-pix_fmt",
        "yuv420p",
        fixture
      ]);
    } catch {
      fs.writeFileSync(fixture, Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from("ftypmp42"), Buffer.alloc(48)]));
    }
  }
  return fixture;
}

test("frontend persists secure video ingestion and exposes explicit processing modes", async ({ page }, testInfo) => {
  const fixture = legalFixture();
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByLabel("Project name")).toBeVisible();
  await expect(page.locator(".save-state")).toContainText("Saved");
  const name = `Milestone 6B ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Location").fill("Bangkok");
  await page.getByRole("button", { name: "Create project", exact: true }).last().click();
  await expect(page.getByRole("button", { name: "Select local video" })).toBeVisible({ timeout: 15000 });

  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Select local video" }).click();
  await (await chooser).setFiles(fixture);
  await expect(page.getByText("e2e-legal-upload.mp4").first()).toBeVisible();
  await expect(page.getByText(/Fingerprint/)).toBeVisible();
  await expect(page.locator(".evidence .badge").filter({ hasText: "Reference ready" })).toBeVisible();
  await expect(page.getByLabel("Source video preview")).toBeVisible();
  await page.getByLabel("Timezone").fill("Asia/Bangkok");
  await page.getByRole("button", { name: "Confirm time window" }).click();
  await expect(page.getByText("Stale result")).toHaveCount(0);

  await page.getByLabel("Side A name").fill("Northbound");
  await page.getByLabel("Side B name").fill("Southbound");
  await page.getByRole("button", { name: "Add line" }).click();
  await page.getByLabel("Counting line name").fill("Ramp crossing");
  await page.getByLabel("Side A name").fill("Eastbound");
  await page.getByLabel("Side B name").fill("Westbound");
  await page.getByRole("button", { name: "Swap side names" }).click();
  await expect(page.getByLabel("Side A name")).toHaveValue("Westbound");
  await expect(page.getByLabel("Side B name")).toHaveValue("Eastbound");
  await page.getByRole("button", { name: "Delete counting line" }).click();
  await expect(page.getByText("Ramp crossing")).toHaveCount(0);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("button", { name: /Counting line 2 Ramp crossing/ })).toBeVisible();
  await page.getByRole("button", { name: "Save scene" }).click();
  await expect(page.locator(".evidence .badge").filter({ hasText: "Reference ready" })).toBeVisible();
  await expect(page.locator(".scene-editor .section-heading .badge").filter({ hasText: "Scene saved" })).toBeVisible();
  await expect(page.getByText("Stale result")).toHaveCount(0);

  await page.getByRole("button", { name: "Operational settings", exact: true }).click();
  const realPreviewButton = page.getByRole("button", { name: "Run real-video preview", exact: true });
  await expect(realPreviewButton).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("Real-video preview is unavailable: start or restart the processing worker and wait for a fresh heartbeat.");
  await expect(page.getByRole("button", { name: "Run synthetic validation preview", exact: true })).toHaveCount(0);
  const previewScreenshotRoot = path.join(repoRoot, ".local-data", "validation", "pilot_preview_remediation");
  fs.mkdirSync(previewScreenshotRoot, { recursive: true });
  const previewViewport = page.viewportSize();
  await page.screenshot({
    path: path.join(previewScreenshotRoot, `${testInfo.project.name}-real-blocked-en-${previewViewport?.width ?? "unknown"}x${previewViewport?.height ?? "unknown"}.png`),
    fullPage: true
  });
  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(page.getByRole("button", { name: "เริ่มพรีวิววิดีโอจริง", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("ระบบพรีวิววิดีโอจริงยังไม่พร้อม");
  await page.screenshot({
    path: path.join(previewScreenshotRoot, `${testInfo.project.name}-real-blocked-th-${previewViewport?.width ?? "unknown"}x${previewViewport?.height ?? "unknown"}.png`),
    fullPage: true
  });
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByRole("button", { name: "Back to workspace", exact: true }).click();

  await expect(page.getByText(/Synthetic validation output is for workflow testing only/)).toBeVisible();
  await expect(page.getByLabel("Processing mode")).toBeVisible();
  await page.getByRole("button", { name: "Start processing job", exact: true }).click();
  await expect(page.getByText(/Synthetic validation output is for workflow testing only/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Certify this result version/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Create CSV manifest/ })).toBeDisabled();

  await page.reload();
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByText("e2e-legal-upload.mp4").first()).toBeVisible();
  await page.getByRole("button", { name: /Ramp crossing/ }).click();
  await expect(page.getByLabel("Side A name")).toHaveValue("Westbound");
  await expect(page.getByLabel("Side B name")).toHaveValue("Eastbound");
  await page.getByRole("button", { name: "TH", exact: true }).click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
  const viewport = page.viewportSize();
  const screenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6b",
    `${testInfo.project.name}-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`
  );
  fs.mkdirSync(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: true });
});

test("operational settings validate an immutable profile and keep preview evidence bounded", async ({ page }, testInfo) => {
  const apiBase = "http://127.0.0.1:8000";
  const unique = Date.now();
  const projectResponse = await page.request.post(`${apiBase}/api/v1/projects`, {
    data: { name: `Milestone 6D browser ${unique}`, location: "Bangkok", study_type: "intersection", language: "en" }
  });
  expect(projectResponse.ok()).toBe(true);
  const project = await projectResponse.json();
  const sourceResponse = await page.request.post(`${apiBase}/api/v1/sources`, {
    data: {
      project_id: project.id,
      file_name: "approved-mock.mp4",
      fingerprint_sha256: "mock-full-file-sha256",
      source_started_at: "2026-01-01T00:00:00+00:00",
      timezone_name: "Asia/Bangkok",
      analysis_start_pts_ms: 0,
      analysis_end_pts_ms: 180000
    }
  });
  expect(sourceResponse.ok()).toBe(true);
  const sceneResponse = await page.request.post(`${apiBase}/api/v1/scenes`, {
    data: { project_id: project.id, template: "intersection" }
  });
  expect(sceneResponse.ok()).toBe(true);
  const scene = await sceneResponse.json();

  await page.addInitScript((projectId) => window.localStorage.setItem("tva.projectId", projectId), project.id);
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByRole("button", { name: "Operational settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Processing profiles and capability check" })).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".profile-card-code", { hasText: "BALANCED" })).toBeVisible();
  await page.getByRole("button", { name: "Validate configuration", exact: true }).click();
  await expect(page.getByText("Configuration is valid for this source window.")).toBeVisible({ timeout: 15000 });
  await page.getByRole("button", { name: "Save immutable revision", exact: true }).click();
  await expect(page.getByText("Immutable configuration revision saved.")).toBeVisible({ timeout: 15000 });
  const previewRequestPromise = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith(`/api/v1/projects/${project.id}/previews`));
  await page.getByRole("button", { name: "Run synthetic validation preview", exact: true }).click();
  const previewRequest = await previewRequestPromise;
  expect(previewRequest.postDataJSON()).toMatchObject({
    mode: "SYNTHETIC",
    fixture_id: "api-acceptance",
    expected_scene_version: scene.version,
    start_pts_ms: 0,
    end_pts_ms: 30000
  });
  await expect(page.getByRole("status").getByText(/Preview completed\. This is PREVIEW_ONLY/)).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/No accuracy metric is shown without approved ground truth/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  const viewport = page.viewportSize();
  const screenshotRoot = path.join(repoRoot, ".local-data", "validation", "milestone_6d");
  fs.mkdirSync(screenshotRoot, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotRoot, `${testInfo.project.name}-en-settings-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`),
    fullPage: true
  });
  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(page.getByRole("heading", { name: "โปรไฟล์การประมวลผลและการตรวจสอบความสามารถ" })).toBeVisible();
  await expect(page.getByRole("status").getByText(/พรีวิวเสร็จแล้ว/)).toBeVisible();
  await page.screenshot({
    path: path.join(screenshotRoot, `${testInfo.project.name}-th-settings-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`),
    fullPage: true
  });
  expect(scene.version).toBeGreaterThan(0);
});

test("invalid time range is rejected through the real API", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByLabel("Project name")).toBeVisible();
  await expect(page.locator(".save-state")).toContainText("Saved");
  const name = `Invalid range ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Location").fill("Bangkok");
  await page.getByRole("button", { name: "Create project", exact: true }).last().click();
  await expect(page.getByRole("button", { name: "Use mock source" })).toBeVisible({ timeout: 15000 });
  await page.getByRole("button", { name: "Use mock source" }).click();
  await expect(page.getByText("approved-mock.mp4")).toBeVisible();
  await page.getByLabel("Analysis end PTS ms").fill("3600000");
  await expect(page.getByLabel("Analysis end PTS ms")).toHaveValue("3600000");
  await page.waitForTimeout(500);
  await page.getByLabel("Analysis end PTS ms").fill("0");
  await expect(page.getByLabel("Analysis end PTS ms")).toHaveValue("0");
  await page.getByRole("button", { name: "Confirm time window" }).click();
  await expect(page.getByText(/analysis range|api_failure_422/)).toBeVisible();
});

test("English and reduced motion states are visible", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "EN", exact: true })).toBeVisible();
  const thaiScreenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6b",
    `${testInfo.project.name}-thai-onboarding.png`
  );
  fs.mkdirSync(path.dirname(thaiScreenshot), { recursive: true });
  await page.screenshot({ path: thaiScreenshot, fullPage: true });
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByLabel("Project name").focus();
  await expect(page.getByLabel("Project name")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Location")).toBeFocused();
  await expect(page.getByRole("button", { name: "Start processing job", exact: true })).toBeVisible();
  const duration = await page.locator("button").first().evaluate((node) => getComputedStyle(node).transitionDuration);
  expect(duration).toContain("0s");
});

test("engineering exclusion diagnostics remain visible in the browser", async ({ page }, testInfo) => {
  const apiBase = "http://127.0.0.1:8000";
  const unique = Date.now();
  const projectResponse = await page.request.post(`${apiBase}/api/v1/projects`, {
    data: { name: `Milestone 6B browser ${unique}`, location: "Bangkok", study_type: "intersection", language: "en" }
  });
  expect(projectResponse.ok()).toBe(true);
  const project = await projectResponse.json();
  const sourceResponse = await page.request.post(`${apiBase}/api/v1/sources`, {
    data: {
      project_id: project.id,
      file_name: "milestone-6b-browser.mp4",
      fingerprint_sha256: `milestone-6b-browser-${unique}`,
      source_started_at: "2026-01-01T00:00:00+00:00",
      timezone_name: "Asia/Bangkok",
      analysis_start_pts_ms: 0,
      analysis_end_pts_ms: 900000
    }
  });
  expect(sourceResponse.ok()).toBe(true);
  const sceneResponse = await page.request.post(`${apiBase}/api/v1/scenes`, {
    data: { project_id: project.id, template: "intersection" }
  });
  expect(sceneResponse.ok()).toBe(true);
  const scene = await sceneResponse.json();
  const runResponse = await page.request.post(`${apiBase}/api/v1/projects/${project.id}/synthetic-runs`, {
    data: { fixture_id: `milestone-6b-browser-${unique}`, expected_scene_version: scene.version }
  });
  expect(runResponse.ok()).toBe(true);

  await page.route("**/api/v1/runs/*/engineering-summary", async (route) => {
    const upstream = await route.fetch();
    const body = await upstream.json();
    body.engineering_ready = false;
    body.result_status = "STRUCTURALLY_INVALID";
    body.reconciliation = {
      ...body.reconciliation,
      status: "STRUCTURALLY_INVALID",
      engineering_ready: false,
      invalid_direction_exclusions: [
        {
          event_id: "evt-browser-invalid-direction",
          technical_key: "browser-invalid-direction",
          original_direction: "northbound",
          expected_domain: ["A_TO_B", "B_TO_A"],
          expected_count: 1,
          resulting_count: 0,
          resulting_count_difference: -1,
          reason: "invalid_canonical_direction"
        }
      ],
      classification_exclusions: [
        {
          event_id: "evt-browser-classification-mismatch",
          technical_key: "browser-classification-mismatch",
          classification_status: "INSUFFICIENT_EVIDENCE",
          engineering_class: "PASSENGER_VEHICLE",
          allowed_classes: ["UNKNOWN"],
          resulting_count_difference: -1,
          reason: "classification_status_class_mismatch:INSUFFICIENT_EVIDENCE:PASSENGER_VEHICLE"
        }
      ]
    };
    body.line_class_totals = [
      { line_id: "line_main", line_name: "Synthetic validation line", engineering_class: "UNKNOWN", total: 1 },
      { line_id: "line_main", line_name: "Synthetic validation line", engineering_class: "AMBIGUOUS", total: 1 }
    ];
    await route.fulfill({ response: upstream, body: JSON.stringify(body) });
  });
  await page.addInitScript((projectId) => window.localStorage.setItem("tva.projectId", projectId), project.id);
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByText("Structurally invalid")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/Invalid source directions are excluded/)).toBeVisible();
  await expect(page.getByText(/Classification status\/class inconsistencies are excluded/)).toBeVisible();
  const viewport = page.viewportSize();
  const screenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6b",
    `${testInfo.project.name}-en-structurally-invalid.png`
  );
  fs.mkdirSync(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: true });
  expect(viewport?.width).toBeGreaterThanOrEqual(1280);
});

test("benchmark workspace exposes an explicit rights-aware empty state", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByRole("button", { name: "Benchmark workspace", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Accuracy and calibration workspace" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No benchmark corpus is loaded" })).toBeVisible();
  const inspector = page.getByRole("complementary", { name: "Benchmark workspace" });
  await expect(inspector.getByText("No owner-approved threshold policy is configured.")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
  const viewport = page.viewportSize();
  const screenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6c",
    `${testInfo.project.name}-benchmark-empty-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`
  );
  fs.mkdirSync(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: true });

  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(page.getByRole("heading", { name: "พื้นที่ประเมินความแม่นยำและปรับเทียบ" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "ยังไม่มีคลังทดสอบ" })).toBeVisible();
  const thaiScreenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6c",
    `${testInfo.project.name}-benchmark-empty-th-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`
  );
  await page.screenshot({ path: thaiScreenshot, fullPage: true });
});

test("benchmark workspace exposes threshold evidence and a completed-not-qualified state", async ({ page }, testInfo) => {
  const latestRun = {
    id: "benchmark-remediation-run",
    benchmark_source_id: "synthetic-holdout-01",
    benchmark_split: "HOLDOUT",
    rights_status: "CLEARED_FOR_REPOSITORY_DISTRIBUTION",
    ground_truth_revision: "synthetic-gt-holdout-remediation-v1",
    evaluation_configuration_revision: "eval-remediation-1",
    status: "COMPLETED",
    qualification_status: "NOT_QUALIFIED",
    source_fingerprint_sha256: "b".repeat(64),
    scene_revision: "synthetic-scene-holdout-v1",
    code_commit_sha: "acceptance-fixture-commit-6c",
    configuration_hash: "acceptance-fixture-config-6c-failing",
    started_at: "2026-08-05T00:00:00Z"
  };
  await page.route("**/api/v1/benchmarks/overview", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "READY",
        corpus_revisions: [{ id: "corpus-1", revision: "synthetic-6c-corpus-v1", sources: [{ id: "source-1", benchmark_source_id: "synthetic-holdout-01", benchmark_split: "HOLDOUT", rights_status: "CLEARED_FOR_REPOSITORY_DISTRIBUTION", condition_tags: ["night", "glare"], checksum_verified: true }] }],
        source_count: 1,
        rights_coverage: { CLEARED_FOR_REPOSITORY_DISTRIBUTION: 1 },
        split_counts: { HOLDOUT: 1 },
        latest_run: latestRun,
        benchmark_runs: [latestRun],
        limitations: []
      })
    });
  });
  await page.route("**/api/v1/benchmarks/runs/benchmark-remediation-run/metrics", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        benchmark_run_id: latestRun.id,
        metrics: {
          event_metrics: { precision: 1, recall: 1, f1: 1, false_positive: 0, false_negative: 0, duplicate_automatic: 0 },
          count_metrics: { total: { automatic_count: 1, ground_truth_count: 1, signed_error: 0 } },
          direction_metrics: { direction_accuracy: 1, direction_errors: 0, denominator: 1 },
          class_metrics: { macro_f1: 1, weighted_f1: 1, unknown_ambiguous_rate: 0, matched_support: 1 },
          timestamp_metrics: { mean_absolute_error_ms: 0, p95_absolute_error_ms: 0, time_authority: "source_relative_pts_ms" },
          fragmentation_metrics: { availability: "NO_MATCHED_IDENTITIES", short_track_count: 0 },
          throughput_metrics: { processing_duration_video_duration_ratio: null, real_time_criterion: "NOT_CLAIMED", resolved_device: "CPU" },
          duplicate_metrics: { missed_events: 0 }
        },
        qualification: {
          status: "NOT_QUALIFIED",
          threshold_policy_present: true,
          policy: { policy_revision: "owner-policy-failing-v1" },
          gates: { required_thresholds_pass: { passed: false, reason: "all mandatory threshold comparisons must pass" } },
          threshold_evaluation: { results: [{ metric_path: "event_metrics.recall", observed_value: 1, required_value: 1.1, operator: "GTE", unit: "ratio", availability: "AVAILABLE", passed: false, failure_reason: "observed value does not satisfy GTE" }] }
        }
      })
    });
  });
  await page.route("**/api/v1/benchmarks/runs/benchmark-remediation-run/matches**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ benchmark_run_id: latestRun.id, items: [], limit: 100, offset: 0, category: null }) });
  });
  await page.route("**/api/v1/benchmarks/experiments**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByRole("button", { name: "Benchmark workspace", exact: true }).click();
  const inspector = page.getByRole("complementary", { name: "Benchmark workspace" });
  await expect(page.getByRole("heading", { name: "NOT_QUALIFIED" }).first()).toBeVisible();
  await expect(page.getByText("owner-policy-failing-v1")).toBeVisible();
  await expect(page.getByText("event_metrics.recall")).toBeVisible();
  await expect(page.getByText("HOLD").first()).toBeVisible();
  await expect(inspector.getByText("No owner-approved threshold policy is configured.")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  const viewport = page.viewportSize();
  const screenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6c",
    `${testInfo.project.name}-benchmark-policy-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`
  );
  fs.mkdirSync(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: true });
  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(page.getByText("owner-policy-failing-v1")).toBeVisible();
  const thaiScreenshot = path.join(
    repoRoot,
    ".local-data",
    "validation",
    "milestone_6c",
    `${testInfo.project.name}-benchmark-policy-th-${viewport?.width ?? "unknown"}x${viewport?.height ?? "unknown"}.png`
  );
  await page.screenshot({ path: thaiScreenshot, fullPage: true });
});
