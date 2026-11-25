from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.api.jobs import _session
from app.services.jobs import JobError
from app.services.filenames import download_filename
from app.services.storage import job_file


router = APIRouter()


@router.get("/download/{job_id}")
def download(job_id: UUID, request: Request):
    manager = request.app.state.job_manager
    record = manager.get(job_id, _session(request))
    if record["state"] != "completed":
        raise JobError(409, "not_completed", "The job is not completed")
    try:
        path = job_file("output", job_id, record["output_filename"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > manager.settings.media_max_output_bytes:
            raise ValueError("Invalid output")
    except (ValueError, OSError, TypeError):
        raise JobError(404, "file_not_found", "Output file is unavailable") from None
    media_types = {"mp4": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "m4a": "audio/mp4", "opus": "audio/ogg"}
    try:
        filename = download_filename(record.get("source_title"), record.get("media_id"), record["format"])
    except ValueError:
        raise JobError(404, "file_not_found", "Output file is unavailable") from None
    return FileResponse(path, filename=filename, media_type=media_types.get(record["format"], "application/octet-stream"), headers={"Cache-Control": "private, no-store"})
