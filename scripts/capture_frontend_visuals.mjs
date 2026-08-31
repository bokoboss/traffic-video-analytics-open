import { chromium } from "@playwright/test";
import path from "node:path";

const out = path.resolve(".local-data/validation/milestone_0_1");
const cases = [
  { name: "thai-1440", width: 1440, height: 900, lang: "th", stale: false },
  { name: "thai-1280", width: 1280, height: 720, lang: "th", stale: false },
  { name: "english-1440", width: 1440, height: 900, lang: "en", stale: false },
  { name: "english-1280", width: 1280, height: 720, lang: "en", stale: false },
  { name: "stale-1440", width: 1440, height: 900, lang: "en", stale: true },
  { name: "states-1280", width: 1280, height: 720, lang: "th", stale: false }
];

const browser = await chromium.launch();
for (const item of cases) {
  const page = await browser.newPage({ viewport: { width: item.width, height: item.height } });
  await page.goto("http://127.0.0.1:5174", { waitUntil: "networkidle" });
  if (item.lang === "en") await page.getByRole("button", { name: "EN", exact: true }).click();
  if (item.stale) await page.getByRole("button", { name: "Simulate stale change" }).click();
  if (item.name === "states-1280") await page.locator("#gallery-title").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(out, `${item.name}.png`), fullPage: true });
  await page.close();
}
await browser.close();
