from pathlib import Path
from uuid import UUID
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

from app.core.config import PROJECT_ROOT, Settings, project_path


def job_directory(kind: str, job_id: UUID) -> Path:
    if kind not in {"temp", "output"}:
        raise ValueError("Invalid storage kind")
    data_root = PROJECT_ROOT / "data"
    kind_root = data_root / kind
    candidate = kind_root / str(job_id)
    if any(path.is_symlink() for path in (data_root, kind_root, candidate)):
        raise ValueError("Storage path cannot contain a symlink")
    return project_path(candidate)


def job_file(kind: str, job_id: UUID, filename: str) -> Path:
    if not filename or filename in {".", ".."} or Path(filename).name != filename or any(char in filename for char in "\\/\x00"):
        raise ValueError("Invalid filename")
    candidate = job_directory(kind, job_id) / filename
    if candidate.is_symlink():
        raise ValueError("Job file cannot be a symlink")
    path = project_path(candidate)
    if path.parent != job_directory(kind, job_id):
        raise ValueError("Invalid storage path")
    return path


def prepare_storage(settings: Settings) -> None:
    if any(path.is_symlink() for path in (PROJECT_ROOT / "data", PROJECT_ROOT / "data/temp", PROJECT_ROOT / "data/output", PROJECT_ROOT / "data/jobs")):
        raise ValueError("Storage root cannot be a symlink")
    for directory in (settings.temp_dir, settings.output_dir, settings.jobs_dir):
        if Path(directory).is_symlink():
            raise ValueError("Storage directory cannot be a symlink")
        path = project_path(directory)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)


def record_path(settings: Settings, job_id: UUID) -> Path:
    return project_path(settings.jobs_dir / f"{job_id}.json")


def save_record(settings: Settings, job_id: UUID, record: dict) -> None:
    destination = record_path(settings, job_id)
    descriptor, temporary = tempfile.mkstemp(prefix="job-", suffix=".tmp", dir=settings.jobs_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_records(settings: Settings) -> list[dict]:
    records = []
    for path in settings.jobs_dir.glob("*.json"):
        if path.is_symlink():
            continue
        try:
            UUID(path.stem)
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return records


def remove_job_files(job_id: UUID, kind: str) -> None:
    directory = job_directory(kind, job_id)
    if directory.is_symlink():
        raise ValueError("Job directory cannot be a symlink")
    if directory.is_dir():
        shutil.rmtree(directory)


def cleanup_expired(settings: Settings) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - settings.media_output_retention_hours * 3600
    for directory in settings.output_dir.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            UUID(directory.name)
            if directory.stat().st_mtime < cutoff:
                remove_job_files(UUID(directory.name), "output")
        except (ValueError, OSError):
            continue
