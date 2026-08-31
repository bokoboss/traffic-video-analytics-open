from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable

from fastapi import FastAPI, Request, Response


DIAGNOSTICS_ENV = "TRAFFIC_APP_DIAGNOSTICS"
LOGGER_NAME = "traffic_video_analytics.diagnostics"


def diagnostics_enabled() -> bool:
    return os.getenv(DIAGNOSTICS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def emit_diagnostic_event(event: str, **payload: object) -> None:
    if not diagnostics_enabled():
        return
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info(json.dumps({"event": event, **payload}, sort_keys=True))


def install_diagnostics(app: FastAPI) -> None:
    if not diagnostics_enabled():
        return

    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @app.middleware("http")
    async def timing_middleware(request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            if "response" in locals():
                response.headers["X-TVA-Request-ID"] = request_id
                response.headers["Server-Timing"] = f"app;dur={elapsed_ms}"
            logger.info(
                json.dumps(
                    {
                        "event": "api_request_timing",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": elapsed_ms,
                    },
                    sort_keys=True,
                )
            )
