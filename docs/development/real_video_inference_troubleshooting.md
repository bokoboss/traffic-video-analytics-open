# Real Video Inference: Provisioning and Troubleshooting

## Provisioning

The normal application setup does not download model weights or AI packages.
The optional runtime is kept in the ignored `.venv-ai` directory, model files
in ignored `.local-tools/models`, and local smoke artifacts in ignored
`.local-data/ai-artifacts`.

The approved local checks are:

```powershell
python scripts/check_media_runtime.py --strict
python scripts/check_ai_runtime.py --strict
```

The primary YOLO11n weight must match the SHA-256 recorded in
`model_registry.json`. Do not commit the weight, customer videos, generated
frames, exports or local databases.

## Pilot run

```powershell
python scripts/run_real_video_pilot.py
```

The harness creates a temporary local database and media root below
`.local-data`, submits `REAL_VIDEO`, runs one separate worker claim and prints
the terminal job plus event provenance. Use `--keep-data` only when inspecting
the temporary evidence locally.

## Common blockers

| Symptom | Check | Meaning |
|---|---|---|
| `real_video_not_ready:*` | `/api/v1/readiness` | A required local runtime, weight checksum or media tool is unavailable. |
| `weight_hash_mismatch` | `model_registry.json` and model file | The file is not the approved registry artifact. |
| `source_media_unavailable` | managed upload and `TVA_LOCAL_DATA_DIR` | The worker will not follow arbitrary or symlinked paths. |
| `missing_frame_pts` | FFmpeg/ffprobe pair and input stream | Authoritative timestamps were not available; the job fails closed. |
| `WORKER_LOST` | worker log and job lease | The worker stopped heartbeating; retry creates a new run. |
| `CANCELLED` with no result | job progress and cancellation record | Expected cooperative cancellation; partial automatic events are discarded. |

## Interpretation limits

The local pilot proves runtime wiring and persistence only. It does not prove
TIMS classification quality, event recall, duplicate suppression quality or
real-time performance. A completed job remains provisional and requires human
review.
