import asyncio
import hashlib
import json
import logging
import os
import secrets
import signal
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.core.config import PROJECT_ROOT, Settings
from app.core.diagnostics import failure_message, safe_diagnostic
from app.schemas.api import CreateJobRequest, JobResponse
from app.services.encoders import REQUIRED_ENCODERS, detect_encoders, unsupported_reason
from app.services.formats import select_streams
from app.services.metadata import AnalysisSnapshot
from app.services.storage import cleanup_expired, job_file, load_records, prepare_storage, remove_job_files, save_record


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def worker_capabilities(settings: Settings) -> tuple[str | None, frozenset[str], str | None]:
    encoder_reason = None
    if not settings.ffmpeg_path.is_file() or not os.access(settings.ffmpeg_path, os.X_OK):
        encoders = frozenset()
        encoder_reason = "FFmpeg is unavailable"
    else:
        try:
            encoders = detect_encoders(settings)
        except (OSError, subprocess.SubprocessError):
            encoders = frozenset()
            encoder_reason = "FFmpeg encoder check failed"
    if not settings.media_worker_enabled:
        return "Media worker is disabled", encoders, encoder_reason
    if os.geteuid() != 0:
        return "Root media worker requires a root API process", encoders, encoder_reason
    if encoder_reason:
        return encoder_reason, encoders, encoder_reason
    if not settings.ffprobe_path.is_file() or not os.access(settings.ffprobe_path, os.X_OK):
        return "ffprobe is unavailable", encoders, encoder_reason
    if not any(not unsupported_reason(output_format, encoders) for output_format in REQUIRED_ENCODERS):
        return "No supported FFmpeg output encoders are available", encoders, encoder_reason
    check = "import os,sys,app.workers.converter; paths=sys.argv[1:]; sys.exit(0 if all(os.access(path, os.R_OK|os.X_OK) for path in paths[:3]) and all(os.access(path, os.W_OK|os.X_OK) for path in paths[3:]) else 1)"
    try:
        result = subprocess.run(
            [str(PROJECT_ROOT / "venv/bin/python"), "-B", "-c", check, str(PROJECT_ROOT), str(settings.ffmpeg_path), str(settings.ffprobe_path), str(settings.temp_dir), str(settings.output_dir)],
            cwd=PROJECT_ROOT, env={"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(settings.temp_dir)},
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "The worker cannot access required project paths", encoders, encoder_reason
    if result.returncode != 0:
        return "The worker cannot read tools or write media directories", encoders, encoder_reason
    return None, encoders, encoder_reason


def worker_unavailable_reason(settings: Settings) -> str | None:
    return worker_capabilities(settings)[0]


def _owner_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def select_source(snapshot: AnalysisSnapshot, request: CreateJobRequest) -> tuple[str, str | None]:
    if snapshot.media.media_id != request.media_id or snapshot.formats.media_id != request.media_id:
        raise JobError(422, "unsupported_source", "The analysis does not match the requested media")
    options = snapshot.formats.options
    matching = [option for option in options if option.format == request.format and option.resolution == request.resolution and option.audio_bitrate_kbps == request.audio_bitrate_kbps]
    if not matching:
        raise JobError(422, "unsupported_output", "The requested output is not available for this source")
    height = int(request.resolution[:-1]) if request.resolution else None
    selected = select_streams(snapshot.formats.formats, request.format, height)
    if not selected:
        raise JobError(422, "unsupported_output", "No usable source stream exists for this output")
    return selected[0].format_id, selected[1].format_id if selected[1] else None


class JobManager:
    TRANSITIONS = {
        "queued": {"downloading", "failed", "cancelled"},
        "downloading": {"processing", "failed", "cancelled"},
        "processing": {"completed", "failed", "cancelled"},
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ready_reason, self.encoders, self.encoder_reason = worker_capabilities(settings)
        prepare_storage(settings)
        self.records: dict[str, dict] = {}
        for record in load_records(settings):
            job_id = record.get("job_id")
            try:
                UUID(job_id)
            except (ValueError, TypeError):
                continue
            if record.get("state") in {"queued", "downloading", "processing"}:
                record.update(state="failed", error="Interrupted by service restart", finished_at=_now(), version=record.get("version", 0) + 1)
                save_record(settings, UUID(job_id), record)
                for kind in ("temp", "output"):
                    try:
                        remove_job_files(UUID(job_id), kind)
                    except (OSError, ValueError):
                        pass
            self.records[job_id] = record
        if settings.media_worker_enabled:
            cleanup_expired(settings)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.active: dict[str, asyncio.subprocess.Process] = {}
        self.terminating_pids: set[int] = set()
        self.terminated_pids: set[int] = set()
        self.termination_reasons: dict[str, str] = {}
        self.spawn_events: dict[str, asyncio.Event] = {}
        self.dispatcher: asyncio.Task | None = None
        self.cleaner: asyncio.Task | None = None

    def start(self) -> None:
        if self.dispatcher is None:
            self.dispatcher = asyncio.create_task(self._dispatch())
        if self.cleaner is None and self.settings.media_worker_enabled:
            self.cleaner = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            cleanup_expired(self.settings)

    async def stop(self) -> None:
        for job_id, process in list(self.active.items()):
            try:
                self.termination_reasons[job_id] = "application_shutdown"
                await self._terminate(process)
            except (OSError, asyncio.TimeoutError):
                continue
            self._update(job_id, state="failed", error="Interrupted by service shutdown", finished_at=_now())
        if self.dispatcher is not None:
            self.dispatcher.cancel()
            try:
                await self.dispatcher
            except asyncio.CancelledError:
                pass
        if self.cleaner is not None:
            self.cleaner.cancel()
            try:
                await self.cleaner
            except asyncio.CancelledError:
                pass

    def _update(self, job_id: str, **changes) -> dict:
        record = self.records[job_id]
        if record["state"] in {"completed", "failed", "cancelled"}:
            return record
        next_state = changes.get("state", record["state"])
        if next_state != record["state"] and next_state not in self.TRANSITIONS.get(record["state"], set()):
            raise JobError(409, "invalid_transition", "Invalid job state transition")
        record.update(changes)
        record["version"] = record.get("version", 0) + 1
        save_record(self.settings, UUID(job_id), record)
        return record

    def _authorized(self, job_id: UUID, token: str | None) -> dict:
        record = self.records.get(str(job_id))
        if not record or not token or not secrets.compare_digest(record.get("owner_hash", ""), _owner_hash(token)) or self._expired(record):
            raise JobError(404, "job_not_found", "Job not found")
        return record

    def _expired(self, record: dict) -> bool:
        try:
            created = datetime.fromisoformat(record["created_at"]).timestamp()
            return datetime.now(timezone.utc).timestamp() > created + self.settings.media_session_ttl_hours * 3600
        except (ValueError, KeyError):
            return True

    def get(self, job_id: UUID, token: str | None) -> dict:
        return self._authorized(job_id, token)

    def list(self, token: str | None) -> list[dict]:
        if not token:
            return []
        owner = _owner_hash(token)
        return sorted((item for item in self.records.values() if secrets.compare_digest(item.get("owner_hash", ""), owner) and not self._expired(item)), key=lambda item: item["created_at"], reverse=True)[:20]

    def public(self, record: dict) -> JobResponse:
        fields = JobResponse.model_fields
        data = {key: value for key, value in record.items() if key in fields}
        if record["state"] == "completed":
            data["download_url"] = f"/api/download/{record['job_id']}"
        return JobResponse(**data)

    def create(self, request: CreateJobRequest, snapshot: AnalysisSnapshot, token: str) -> dict:
        if self.ready_reason:
            raise JobError(503, "worker_unavailable", self.ready_reason)
        encoder_reason = unsupported_reason(request.format.value, self.encoders)
        if encoder_reason:
            raise JobError(422, "unsupported_encoder", encoder_reason)
        if not request.confirm_rights:
            raise JobError(422, "rights_required", "Confirm that you may process this media")
        if request.include_subtitles:
            raise JobError(422, "unsupported_option", "Subtitle inclusion is not supported")
        if snapshot.media.live_status in {"is_live", "is_upcoming", "post_live"} or snapshot.media.availability not in {None, "public"}:
            raise JobError(422, "unsupported_source", "This source is not eligible for conversion")
        if snapshot.media.duration is None or snapshot.media.duration > self.settings.media_max_duration_seconds:
            raise JobError(422, "duration_limit", "Source duration is unavailable or exceeds the limit")
        video_format_id, audio_format_id = select_source(snapshot, request)
        if sum(item["state"] == "queued" for item in self.records.values()) >= self.settings.media_max_queued_jobs:
            raise JobError(503, "queue_full", "The conversion queue is full")
        owner = _owner_hash(token)
        if any(item.get("owner_hash") == owner and item.get("state") in {"queued", "downloading", "processing"} and (item.get("media_id"), item.get("format"), item.get("resolution"), item.get("audio_bitrate_kbps")) == (request.media_id, request.format.value, request.resolution, request.audio_bitrate_kbps) for item in self.records.values()):
            raise JobError(409, "duplicate_job", "An identical conversion job is already active")
        selected_ids = {video_format_id, audio_format_id}
        estimated_bytes = sum((item.filesize or item.filesize_approx or 0) for item in snapshot.formats.formats if item.format_id in selected_ids)
        if estimated_bytes > self.settings.media_max_download_bytes:
            raise JobError(422, "size_limit", "Estimated source size exceeds the limit")
        if shutil.disk_usage(self.settings.temp_dir).free < self.settings.media_max_download_bytes + self.settings.media_max_output_bytes:
            raise JobError(503, "disk_full", "Insufficient disk space for a conversion job")
        job_id = uuid4()
        record = {
            "job_id": str(job_id), "owner_hash": _owner_hash(token), "media_id": request.media_id,
            "source_title": snapshot.media.title,
            "format": request.format.value, "resolution": request.resolution, "audio_bitrate_kbps": request.audio_bitrate_kbps,
            "duration_seconds": snapshot.media.duration,
            "include_metadata": request.include_metadata, "video_format_id": video_format_id, "audio_format_id": audio_format_id,
            "state": "queued", "progress": 0, "download_progress": 0, "conversion_progress": 0,
            "created_at": _now(), "started_at": None, "finished_at": None, "error": None,
            "output_filename": None, "output_bytes": None, "version": 1,
        }
        save_record(self.settings, job_id, record)
        self.records[str(job_id)] = record
        self.queue.put_nowait(str(job_id))
        return record

    async def _dispatch(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                if self.records[job_id]["state"] == "queued":
                    await self._run(job_id)
            finally:
                self.queue.task_done()

    async def _run(self, job_id: str) -> None:
        record = self._update(job_id, state="downloading", started_at=_now())
        spawned = asyncio.Event()
        self.spawn_events[job_id] = spawned
        worker_failure = None
        stderr_task = None
        stage = "download"
        failure_code = None
        worker_pid = None
        process = None
        command = [str(PROJECT_ROOT / "venv/bin/python"), "-m", "app.workers.converter"]
        environment = {
            "PATH": "/opt/bin:/usr/bin:/bin", "PYTHONPATH": str(PROJECT_ROOT), "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(self.settings.temp_dir), "HOME": str(self.settings.temp_dir), "TZ": self.settings.app_timezone,
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=PROJECT_ROOT, env=environment, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
            )
            worker_pid = getattr(process, "pid", None)
            async def read_stderr():
                retained = b""
                while chunk := await process.stderr.read(4096):
                    retained = (retained + chunk)[-4096:]
                return safe_diagnostic(retained.decode("utf-8", errors="replace"))
            stderr_task = asyncio.create_task(read_stderr())
            self.active[job_id] = process
            if record.get("cancel_requested"):
                self.termination_reasons[job_id] = "user_cancel"
                await self._terminate(process)
                self._update(job_id, state="cancelled", finished_at=_now())
                return
            spawned.set()
            payload = {key: record[key] for key in ("job_id", "media_id", "format", "resolution", "audio_bitrate_kbps", "include_metadata", "video_format_id", "audio_format_id")}
            payload.update(ffmpeg_path=str(self.settings.ffmpeg_path), ffprobe_path=str(self.settings.ffprobe_path), max_download_bytes=self.settings.media_max_download_bytes, max_output_bytes=self.settings.media_max_output_bytes, timeout_seconds=self.settings.media_job_timeout_seconds, duration_seconds=record["duration_seconds"])
            process.stdin.write(json.dumps(payload).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            async def read_events():
                nonlocal worker_failure, stage
                while line := await process.stdout.readline():
                    if len(line) > 8192:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if record["state"] in {"cancelled", "failed", "completed"}:
                        continue
                    if event.get("state") == "processing":
                        stage = "transcode"
                        self._update(job_id, state="processing", progress=50, download_progress=100)
                    elif event.get("state") == "downloading":
                        value = max(0, min(100, float(event.get("progress", 0))))
                        self._update(job_id, progress=value / 2, download_progress=value)
                    elif event.get("state") == "processing_progress":
                        stage = "transcode"
                        value = max(0, min(100, float(event.get("progress", 0))))
                        self._update(job_id, state="processing", progress=50 + value / 2, download_progress=100, conversion_progress=value)
                    elif event.get("state") == "completed":
                        stage = "finalize"
                        record["pending_output"] = event.get("output_filename")
                    elif event.get("state") == "failed":
                        worker_failure = event
            await asyncio.wait_for(read_events(), timeout=self.settings.media_job_timeout_seconds)
            code = await asyncio.wait_for(process.wait(), timeout=5)
            await asyncio.wait_for(stderr_task, timeout=1)
            if record["state"] in {"cancelled", "failed"}:
                return
            if record.get("cancel_requested"):
                return
            if code != 0:
                raise RuntimeError("Worker exited unsuccessfully")
            filename = record.pop("pending_output", None)
            if not isinstance(filename, str):
                failure_code = "output_finalization_failed"
                raise RuntimeError("Worker did not report an output")
            failure_code = "output_validation_failed"
            output = job_file("output", UUID(job_id), filename)
            if not output.is_file() or output.is_symlink() or output.stat().st_size > self.settings.media_max_output_bytes:
                raise RuntimeError("Worker output is invalid")
            self._update(job_id, state="completed", progress=100, conversion_progress=100, finished_at=_now(), output_filename=filename, output_bytes=output.stat().st_size)
        except Exception as error:
            if record["state"] not in {"cancelled", "completed"}:
                process = self.active.get(job_id)
                timed_out = isinstance(error, asyncio.TimeoutError) or bool(worker_failure and worker_failure.get("timed_out"))
                if timed_out:
                    self.termination_reasons[job_id] = "timeout"
                elif record.get("cancel_requested"):
                    self.termination_reasons[job_id] = "user_cancel"
                error_code = worker_failure.get("code") if worker_failure else "conversion_timeout" if timed_out else failure_code or "worker_failed"
                if not isinstance(error_code, str):
                    error_code = "worker_failed"
                failure_stage = worker_failure.get("stage") if worker_failure else stage
                failure_tool = worker_failure.get("tool") if worker_failure else "worker"
                diagnostic = worker_failure.get("diagnostic") if worker_failure else None
                if not diagnostic and stderr_task and stderr_task.done() and not stderr_task.cancelled():
                    try:
                        diagnostic = stderr_task.result()
                    except Exception:
                        pass
                if worker_failure:
                    exit_code = worker_failure.get("exit_code")
                    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                        exit_code = None
                else:
                    exit_code = process.returncode if process else None
                logging.getLogger("ytconvert").error("Media job failed", extra={
                    "job_id": job_id, "stage": failure_stage if failure_stage in {"setup", "download", "merge", "probe", "transcode", "finalize"} else stage,
                    "tool": failure_tool if failure_tool in {"worker", "yt-dlp", "ffprobe", "ffmpeg", "filesystem"} else "worker",
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "exception_type": worker_failure.get("exception_type") if worker_failure else type(error).__name__,
                    "diagnostic": safe_diagnostic(diagnostic or error),
                    "worker_pid": worker_pid,
                    "ffmpeg_pid": worker_failure.get("ffmpeg_pid") if worker_failure else None,
                    "ffmpeg_cpu_seconds": worker_failure.get("ffmpeg_cpu_seconds") if worker_failure else None,
                    "cpu_limit_seconds": worker_failure.get("cpu_limit_seconds") if worker_failure else None,
                    "termination_reason": self.termination_reasons.get(job_id, "unknown"),
                    "cancel_requested": bool(record.get("cancel_requested")),
                    "shutdown_requested": self.termination_reasons.get(job_id) == "application_shutdown",
                })
                if process and process.returncode is None:
                    await self._terminate(process)
                self._update(job_id, state="failed", error=failure_message(error_code), finished_at=_now())
        finally:
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            spawned.set()
            self.spawn_events.pop(job_id, None)
            self.active.pop(job_id, None)
            if process:
                self.terminated_pids.discard(getattr(process, "pid", None))
            self.termination_reasons.pop(job_id, None)
            try:
                remove_job_files(UUID(job_id), "temp")
            except (OSError, ValueError):
                pass
            if record["state"] != "completed":
                try:
                    remove_job_files(UUID(job_id), "output")
                except (OSError, ValueError):
                    pass

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.pid in self.terminating_pids or process.pid in self.terminated_pids:
            return
        self.terminating_pids.add(process.pid)
        self.terminated_pids.add(process.pid)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.returncode is None:
                await process.wait()
            self.terminating_pids.discard(process.pid)
            return
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await asyncio.wait_for(process.wait(), timeout=5)
                self.terminating_pids.discard(process.pid)
                return
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            self.terminating_pids.discard(process.pid)
            return
        os.killpg(process.pid, signal.SIGKILL)
        self.terminating_pids.discard(process.pid)

    async def cancel(self, job_id: UUID, token: str | None) -> dict:
        record = self._authorized(job_id, token)
        if record["state"] in {"completed", "failed", "cancelled"}:
            return record
        process = self.active.get(str(job_id))
        spawning = self.spawn_events.get(str(job_id))
        if process is None and spawning is not None:
            record["cancel_requested"] = True
            try:
                await asyncio.wait_for(spawning.wait(), timeout=10)
            except asyncio.TimeoutError:
                raise JobError(503, "cancellation_failed", "The worker could not be stopped") from None
            if record["state"] in {"completed", "failed", "cancelled"}:
                return record
            process = self.active.get(str(job_id))
        if process:
            record["cancel_requested"] = True
            self.termination_reasons[str(job_id)] = "user_cancel"
            try:
                await self._terminate(process)
            except (OSError, asyncio.TimeoutError):
                record.pop("cancel_requested", None)
                raise JobError(503, "cancellation_failed", "The worker could not be stopped") from None
        if record["state"] == "completed":
            return record
        self._update(str(job_id), state="cancelled", finished_at=_now())
        remove_job_files(job_id, "temp")
        return record
