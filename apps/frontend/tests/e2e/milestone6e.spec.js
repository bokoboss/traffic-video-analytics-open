/* global document, window */
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

test("6E review workspace exposes scoped evidence and bilingual audit states", async ({ page }, testInfo) => {
  const apiBase = "http://127.0.0.1:8000";
  const unique = Date.now();
  const projectResponse = await page.request.post(`${apiBase}/api/v1/projects`, {
    data: { name: `Milestone 6E browser ${unique}`, location: "Bangkok", study_type: "intersection", language: "th" }
  });
  expect(projectResponse.ok()).toBe(true);
  const project = await projectResponse.json();
  const sourceResponse = await page.request.post(`${apiBase}/api/v1/sources`, {
    data: {
      project_id: project.id,
      file_name: "milestone-6e-synthetic.mp4",
      fingerprint_sha256: `milestone-6e-${unique}`,
      source_started_at: "2026-01-01T00:00:00+00:00",
      timezone_name: "Asia/Bangkok",
      analysis_start_pts_ms: 0,
      analysis_end_pts_ms: 3_600_000
    }
  });
  expect(sourceResponse.ok()).toBe(true);
  const sceneResponse = await page.request.post(`${apiBase}/api/v1/scenes`, {
    data: { project_id: project.id, template: "intersection" }
  });
  expect(sceneResponse.ok()).toBe(true);
  const scene = await sceneResponse.json();
  const runResponse = await page.request.post(`${apiBase}/api/v1/projects/${project.id}/synthetic-runs`, {
    data: { fixture_id: `milestone-6e-${unique}`, expected_scene_version: scene.version }
  });
  expect(runResponse.ok()).toBe(true);

  await page.addInitScript((projectId) => window.localStorage.setItem("tva.projectId", projectId), project.id);
  await page.goto("/");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  const workspace = page.locator(".review-workspace");
  await expect(workspace.getByRole("heading", { name: "Review, correction and certification queue" })).toBeVisible({ timeout: 15000 });
  await expect(workspace.getByText("Automatic", { exact: true })).toBeVisible();
  await expect(workspace.getByRole("button", { name: "Add missed event" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  const screenshotRoot = path.resolve("..", "..", ".local-data/validation/milestone_6e");
  fs.mkdirSync(screenshotRoot, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotRoot, `${testInfo.project.name}-en.png`),
    fullPage: true
  });

  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(workspace.getByRole("heading", { name: "คิวตรวจสอบ แก้ไข และรับรองผล" })).toBeVisible();
  await workspace.getByRole("button", { name: "หลักฐานเหตุการณ์" }).click();
  await expect(workspace.getByText("เหตุการณ์อัตโนมัติ", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
  await page.screenshot({
    path: path.join(screenshotRoot, `${testInfo.project.name}-th.png`),
    fullPage: true
  });
});
