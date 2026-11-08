import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("request_id", "method", "path", "status_code", "duration_ms", "job_id", "stage", "tool", "exit_code", "timed_out", "exception_type", "diagnostic", "worker_pid", "ffmpeg_pid", "ffmpeg_cpu_seconds", "cpu_limit_seconds", "termination_reason", "cancel_requested", "shutdown_requested"):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    logging.getLogger("uvicorn.access").disabled = True
    logger = logging.getLogger("ytconvert")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
