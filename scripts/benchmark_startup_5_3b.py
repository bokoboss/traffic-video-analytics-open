from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "scripts" / "windows" / "run_app.ps1"
STOP_SCRIPT = ROOT / "scripts" / "windows" / "stop_app.ps1"
FRONTEND_URL = "http://127.0.0.1:5174"
READINESS_URL = "http://127.0.0.1:8000/api/v1/readiness"


@dataclass(frozen=True)
class TimedCommand:
    seconds: float
    returncode: int
    stdout: str
    stderr: str


def run_command(command: Path, timeout_seconds: int, env: dict[str, str], label: str) -> TimedCommand:
    started = time.perf_counter()
    log_dir = ROOT / ".local-data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"benchmark-5-3b-{label}.out.log"
    err_path = log_dir / f"benchmark-5-3b-{label}.err.log"
    with out_path.open("w", encoding="utf-8", errors="replace") as stdout, err_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as stderr:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(command)],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    return TimedCommand(
        seconds=round(time.perf_counter() - started, 3),
        returncode=completed.returncode,
        stdout=out_path.read_text(encoding="utf-8", errors="replace")[-1000:],
        stderr=err_path.read_text(encoding="utf-8", errors="replace")[-1000:],
    )


def wait_json(url: str, timeout_seconds: int) -> tuple[float | None, dict[str, Any] | None]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    while time.perf_counter() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return round(time.perf_counter() - started, 3), payload
        except (OSError, URLError, json.JSONDecodeError):
            time.sleep(0.25)
    return None, None


def wait_http(url: str, timeout_seconds: int) -> float | None:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    while time.perf_counter() < deadline:
        try:
            with urlopen(url, timeout=2):
                return round(time.perf_counter() - started, 3)
        except OSError:
            time.sleep(0.25)
    return None


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"samples": 0, "min": None, "median": None, "max": None}
    return {
        "samples": len(values),
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 5.3B startup/shutdown benchmark.")
    parser.add_argument("--runs", type=int, default=3, help="Clean start/stop sample count.")
    parser.add_argument("--timeout", type=int, default=90, help="Per launcher command timeout in seconds.")
    args = parser.parse_args()

    env = os.environ.copy()
    env["TRAFFIC_APP_DIAGNOSTICS"] = "1"
    env["TVA_NO_BROWSER"] = "1"

    run_samples: list[dict[str, Any]] = []
    stop_before = run_command(STOP_SCRIPT, args.timeout, env, "initial-stop")
    for index in range(args.runs):
        label = f"{index + 1}"
        start = run_command(RUN_SCRIPT, args.timeout, env, f"{label}-start")
        frontend_seconds = wait_http(FRONTEND_URL, 5)
        readiness_seconds, readiness = wait_json(READINESS_URL, 5)
        duplicate = run_command(RUN_SCRIPT, args.timeout, env, f"{label}-duplicate")
        stop = run_command(STOP_SCRIPT, args.timeout, env, f"{label}-stop")
        run_samples.append(
            {
                "label": "cold" if index == 0 else "warm",
                "launcher_seconds": start.seconds,
                "launcher_returncode": start.returncode,
                "launcher_stdout": start.stdout.strip() if start.returncode != 0 else "",
                "launcher_stderr": start.stderr.strip() if start.returncode != 0 else "",
                "frontend_response_after_launcher_seconds": frontend_seconds,
                "readiness_response_after_launcher_seconds": readiness_seconds,
                "readiness_status": readiness.get("status") if readiness else "unavailable",
                "duplicate_start_seconds": duplicate.seconds,
                "duplicate_start_returncode": duplicate.returncode,
                "duplicate_start_stdout": duplicate.stdout.strip() if duplicate.returncode != 0 else "",
                "duplicate_start_stderr": duplicate.stderr.strip() if duplicate.returncode != 0 else "",
                "shutdown_seconds": stop.seconds,
                "shutdown_returncode": stop.returncode,
                "shutdown_stdout": stop.stdout.strip() if stop.returncode != 0 else "",
                "shutdown_stderr": stop.stderr.strip() if stop.returncode != 0 else "",
            }
        )

    report = {
        "benchmark": "milestone_5_3b_startup",
        "runs": run_samples,
        "initial_stop_returncode": stop_before.returncode,
        "summary": {
            "launcher_seconds": summarize([sample["launcher_seconds"] for sample in run_samples]),
            "duplicate_start_seconds": summarize([sample["duplicate_start_seconds"] for sample in run_samples]),
            "shutdown_seconds": summarize([sample["shutdown_seconds"] for sample in run_samples]),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    print(
        "Summary: "
        f"launcher median {report['summary']['launcher_seconds']['median']}s, "
        f"duplicate median {report['summary']['duplicate_start_seconds']['median']}s, "
        f"shutdown median {report['summary']['shutdown_seconds']['median']}s"
    )


if __name__ == "__main__":
    main()
