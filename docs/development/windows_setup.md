# Windows Setup

## Supported Pilot matrix

- Windows 11 x64;
- Python 3.11 or newer;
- approved portable Node runtime in `.local-tools/node`, version >=20.19.0 and
  <27 (Node 24 is recommended; Node 26 is accepted);
- exact pnpm `11.9.0` in `.local-tools/pnpm` or `TVA_PNPM_CMD`;
- paired FFmpeg/FFprobe from `TVA_FFMPEG_DIR`, `.local-tools/ffmpeg/bin`, or a
  controlled `PATH` entry when media operations are needed;
- optional `.venv-ai` and verified model weights only for REAL_VIDEO;
- NVIDIA/CUDA is optional and never implied by core readiness.

`setup_app.bat` does not download runtimes. IT may prepare the approved local
runtime directories before handing the machine to an operator.

## Tested support matrix

The following versions were observed during this Pilot qualification on the
current Windows 11 x64 workstation. A runtime probe is not a real-media
accuracy or throughput qualification.

| Component | Minimum | Tested version | Required / recommended | Fallback | Validation status | Notes |
|---|---|---|---|---|---|---|
| Windows | Windows 11 64-bit | Windows 11 Pro x64 | Required | None in Pilot | PASS | Localhost-only pilot target. |
| Python | 3.11+ | 3.12.10 | Required | None | PASS | Backend, worker and scripts use the repository environment. |
| Node.js | 20.19.0 and <27 | 26.5.0 portable | Required | Approved system PATH | PASS | Node 24 is recommended; Node 26 is accepted after runtime tests. |
| pnpm | 11.9.0 | 11.9.0 | Required | Configured approved command | PASS | Exact version is enforced by setup and launcher helpers. |
| FFmpeg / FFprobe | Paired approved executables | 8.1.2 pair | Required for media operations | Synthetic validation without real media | PASS_WITH_LIMITATION | Pair and version consistency were probed; no field video was processed. |
| SQLite | Migrations through 018 | SQLite 3.49.1 | Required | None | PASS | Backup, restore, migration, heartbeat and persistence tests pass. |
| Chromium browser | Current Chromium family | Playwright Chromium 1.54.1 | Required for UI | Current Edge/Chrome family | PASS | Automated E2E coverage; operator browser support remains local-only. |
| AI runtime | Approved optional environment | PyTorch 2.10.0+cu128, Ultralytics 8.4.103 | Recommended for REAL_VIDEO | CPU where the configured runtime permits | READY_WITH_LIMITATION | Runtime and one detector hash are present; no accuracy claim. |
| NVIDIA / CUDA | None for core UI | RTX A1000 Laptop GPU, driver 573.44, CUDA 12.8 | Recommended | CPU | READY_WITH_LIMITATION | Availability was probed; no hardware-specific throughput claim. |

## First run

1. Run `setup_app.bat`.
2. Confirm the setup report and `.local-data/runtime/setup-complete.json` show
   the release version and migration state.
3. Run `run_app.bat`.
4. Confirm the startup shell shows frontend, backend, storage, video runtime
   and worker states. Missing optional AI must not block project creation.
5. Create a project, add an approved local source and confirm recording time
   and PTS range before scene setup.

The setup process creates only the managed local-data directories, virtual
environment/dependency installation, frontend build and setup marker. It does
not delete projects, media or exports.

## Troubleshooting

- Node/pnpm missing: prepare the approved portable runtime or set
  `TVA_PNPM_CMD`; do not install a different pnpm version silently.
- FFmpeg unavailable: set `TVA_FFMPEG_DIR` to the approved `bin` directory or
  place paired executables under `.local-tools/ffmpeg/bin`.
- AI unavailable: use synthetic validation and record REAL_VIDEO as blocked;
  do not label synthetic output as a detector result.
- setup failure: inspect `.local-data/logs/setup.log` and rerun setup after
  correcting the named prerequisite.
