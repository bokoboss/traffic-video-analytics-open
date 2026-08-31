from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = ROOT / ".local-data" / "validation" / "milestone_5_3c"
RUN_APP = ROOT / "scripts" / "windows" / "run_app.ps1"
STOP_APP = ROOT / "scripts" / "windows" / "stop_app.ps1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Milestone 5.3C media preview and scene interaction.")
    parser.add_argument("--output", type=Path, default=VALIDATION_DIR / "benchmark-5-3c.json")
    parser.add_argument("--keep-running", action="store_true", help="Leave local services running after the benchmark.")
    args = parser.parse_args()

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    node = local_node()
    if node is None:
        print("Node.js runtime unavailable; run setup_app.bat first.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["TRAFFIC_APP_DIAGNOSTICS"] = "1"
    env["TVA_NO_BROWSER"] = "1"
    env["PATH"] = f"{ROOT / '.local-tools' / 'node'}{os.pathsep}{env.get('PATH', '')}"

    stop_services(env)
    start = run_powershell(RUN_APP, env, "start")
    if start.returncode != 0:
        print(start.stdout)
        print(start.stderr, file=sys.stderr)
        return start.returncode

    js_path = VALIDATION_DIR / "benchmark-5-3c-runner.mjs"
    js_path.write_text(browser_runner_js(args.output), encoding="utf-8")
    try:
        result = subprocess.run(
            [str(node), str(js_path)],
            cwd=ROOT,
            env=env,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
        )
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    finally:
        if not args.keep_running:
            stop_services(env)


def local_node() -> Path | None:
    candidate = ROOT / ".local-tools" / "node" / ("node.exe" if os.name == "nt" else "node")
    if candidate.exists():
        return candidate
    resolved = shutil.which("node")
    return Path(resolved) if resolved else None


def run_powershell(script: Path, env: dict[str, str], label: str) -> subprocess.CompletedProcess[str]:
    stdout_path = VALIDATION_DIR / f"benchmark-5-3c-{label}.stdout.log"
    stderr_path = VALIDATION_DIR / f"benchmark-5-3c-{label}.stderr.log"
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            return_code = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = 124
    return subprocess.CompletedProcess(
        [str(script)],
        return_code,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def stop_services(env: dict[str, str]) -> None:
    run_powershell(STOP_APP, env, "stop")


def browser_runner_js(output: Path) -> str:
    output_literal = json.dumps(str(output.resolve()))
    return textwrap.dedent(
        f"""
        import {{ chromium }} from '@playwright/test';
        import fs from 'node:fs';
        import path from 'node:path';
        import {{ execFileSync }} from 'node:child_process';

        const repoRoot = process.cwd();
        const outputPath = {output_literal};
        const outputDir = path.dirname(outputPath);
        fs.mkdirSync(outputDir, {{ recursive: true }});
        const base = 'http://127.0.0.1:5174';
        const apiBase = 'http://127.0.0.1:8000';
        const measuredAt = {json.dumps(datetime.now(timezone.utc).isoformat())};

        function now() {{ return performance.now(); }}
        function round(value) {{ return Number(value.toFixed(3)); }}
        function summary(values) {{
          const sorted = [...values].sort((a, b) => a - b);
          return {{ samples: values.length, min: round(sorted[0]), median: round(sorted[Math.floor(sorted.length / 2)]), max: round(sorted[sorted.length - 1]) }};
        }}
        async function timed(label, fn) {{
          const started = now();
          const value = await fn();
          return {{ label, ms: round(now() - started), value }};
        }}
        function ffmpegPath() {{
          const executable = process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg';
          const local = path.join(repoRoot, '.local-tools', 'ffmpeg', 'bin', executable);
          return fs.existsSync(local) ? local : executable;
        }}
        function fixturePath() {{
          const fixture = path.join(outputDir, 'benchmark-video.mp4');
          if (!fs.existsSync(fixture)) {{
            execFileSync(ffmpegPath(), ['-y', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=10:duration=2', '-pix_fmt', 'yuv420p', fixture], {{ stdio: 'ignore' }});
          }}
          return fixture;
        }}

        const browser = await chromium.launch({{ headless: true }});
        const page = await browser.newPage({{ viewport: {{ width: 1440, height: 900 }} }});
        await page.addInitScript(() => {{
          window.localStorage.setItem('tva.diagnostics', '1');
          window.__tvaFetchLog = [];
          const original = window.fetch;
          window.fetch = async (...args) => {{
            const started = performance.now();
            const url = String(args[0]);
            try {{
              const response = await original(...args);
              window.__tvaFetchLog.push({{ url, status: response.status, ms: performance.now() - started }});
              return response;
            }} catch (error) {{
              window.__tvaFetchLog.push({{ url, status: 0, ms: performance.now() - started }});
              throw error;
            }}
          }};
        }});

        const results = [];
        results.push(await timed('project_open_to_project_field_visible', async () => {{
          await page.goto(base + '/');
          await page.getByRole('button', {{ name: 'EN', exact: true }}).click();
          await page.getByLabel('Project name').waitFor({{ timeout: 20000 }});
        }}));
        const projectName = 'Milestone 5.3C benchmark ' + Date.now();
        await page.getByLabel('Project name').fill(projectName);
        await page.getByLabel('Location').fill('Bangkok');
        results.push(await timed('create_project_to_source_selector_visible', async () => {{
          await page.getByRole('button', {{ name: 'Create project', exact: true }}).last().click();
          await page.getByRole('button', {{ name: 'Select local video' }}).waitFor({{ timeout: 20000 }});
        }}));
        const chooserPromise = page.waitForEvent('filechooser');
        await page.getByRole('button', {{ name: 'Select local video' }}).click();
        const chooser = await chooserPromise;
        results.push(await timed('source_upload_to_first_preview_visible', async () => {{
          await chooser.setFiles(fixturePath());
          await page.getByText('benchmark-video.mp4').first().waitFor({{ timeout: 30000 }});
          await page.locator('.evidence .badge').filter({{ hasText: 'Reference ready' }}).waitFor({{ timeout: 30000 }});
          await page.getByLabel('Source video preview').waitFor({{ timeout: 30000 }});
        }}));

        const projectId = await page.evaluate(() => window.localStorage.getItem('tva.projectId'));
        const snapshot = await (await fetch(`${{apiBase}}/api/v1/projects/${{projectId}}`)).json();
        const source = snapshot.source;
        const frame = snapshot.reference_frame;
        const start = Number(source.analysis_start_pts_ms ?? 0);
        const end = Number(source.analysis_end_pts_ms ?? source.duration_ms ?? 2000);
        const midpoint = Math.round(start + Math.max(0, end - start) / 2);
        const cachedFrame = [];
        for (let index = 0; index < 5; index += 1) {{
          const started = now();
          const response = await fetch(`${{apiBase}}/api/v1/projects/${{projectId}}/reference-frames`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ mode: 'timestamp', requested_pts_ms: midpoint }})
          }});
          await response.json();
          cachedFrame.push(now() - started);
        }}
        results.push({{ label: 'cached_representative_frame_api', ms_summary: summary(cachedFrame) }});
        const forcedFrame = [];
        for (let index = 0; index < 3; index += 1) {{
          const started = now();
          const response = await fetch(`${{apiBase}}/api/v1/projects/${{projectId}}/reference-frames`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ mode: 'timestamp', requested_pts_ms: midpoint + 100 + index, force_regenerate: true }})
          }});
          await response.json();
          forcedFrame.push(now() - started);
        }}
        results.push({{ label: 'forced_representative_frame_extraction_api', ms_summary: summary(forcedFrame) }});
        results.push(await timed('video_range_request_8_bytes', async () => {{
          const response = await fetch(`${{apiBase}}/api/v1/projects/${{projectId}}/source-video`, {{ headers: {{ Range: 'bytes=0-7' }} }});
          await response.arrayBuffer();
          return {{ status: response.status, contentRange: response.headers.get('content-range') }};
        }}));

        const apiBeforeDrag = await page.evaluate(() => window.__tvaFetchLog.length);
        const handle = page.locator('.reference-frame svg circle').last();
        const box = await handle.boundingBox();
        if (box) {{
          const x = box.x + box.width / 2;
          const y = box.y + box.height / 2;
          const started = now();
          await page.mouse.move(x, y);
          await page.mouse.down();
          for (let index = 1; index <= 40; index += 1) {{
            await page.mouse.move(x - index * 3, y + index * 1.5);
          }}
          await page.mouse.up();
          await page.evaluate(() => new Promise(requestAnimationFrame));
          results.push({{ label: 'drag_40_pointer_moves_to_next_frame', ms: round(now() - started) }});
        }}
        const apiAfterDrag = await page.evaluate(() => window.__tvaFetchLog.length);
        results.push({{ label: 'api_calls_during_drag', count: apiAfterDrag - apiBeforeDrag }});
        await page.getByLabel('Side A name').fill('Northbound benchmark side');
        await page.getByLabel('Side B name').fill('Southbound benchmark side');
        results.push(await timed('scene_save_to_saved_badge', async () => {{
          await page.getByRole('button', {{ name: 'Save scene' }}).click();
          await page.locator('.scene-editor .section-heading .badge').filter({{ hasText: 'Scene saved' }}).waitFor({{ timeout: 20000 }});
        }}));
        const fetchLogBeforeReload = await page.evaluate(() => window.__tvaFetchLog);
        const interactionDiagnosticsBeforeReload = await page.evaluate(() => window.__tvaInteractionDiagnostics ?? []);
        results.push(await timed('reload_to_persisted_scene_visible', async () => {{
          await page.reload();
          await page.getByRole('button', {{ name: 'EN', exact: true }}).click();
          await page.getByText('benchmark-video.mp4').first().waitFor({{ timeout: 20000 }});
          await page.getByLabel('Side A name').waitFor({{ timeout: 20000 }});
        }}));
        const viewport = page.viewportSize();
        await page.screenshot({{ path: path.join(outputDir, `benchmark-scene-${{viewport.width}}x${{viewport.height}}.png`), fullPage: true }});
        const fetchLogAfterReload = await page.evaluate(() => window.__tvaFetchLog);
        const interactionDiagnosticsAfterReload = await page.evaluate(() => window.__tvaInteractionDiagnostics ?? []);
        const fetchLog = [...fetchLogBeforeReload, ...fetchLogAfterReload];
        const interactionDiagnostics = [...interactionDiagnosticsBeforeReload, ...interactionDiagnosticsAfterReload];
        await browser.close();
        function sanitizeUrl(url) {{
          return url.replace('http://127.0.0.1:8000', 'localhost').replace('http://127.0.0.1:5174', 'localhost');
        }}
        const report = {{
          schema_version: 'milestone-5-3c-benchmark-v1',
          measured_at: measuredAt,
          project_id: projectId,
          results,
          interaction_diagnostics: interactionDiagnostics,
          fetch_log_summary: fetchLog.map((item) => ({{
            url: sanitizeUrl(item.url),
            status: item.status,
            ms: round(item.ms)
          }}))
        }};
        fs.writeFileSync(outputPath, JSON.stringify(report, null, 2));
        console.log(JSON.stringify(report, null, 2));
        """
    )


if __name__ == "__main__":
    raise SystemExit(main())
